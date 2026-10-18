"""Offline checks for the staged read-only release preflight WorkflowTemplate."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "gitops/release-workflows"
REQUEST_REVISION = "b" * 40
SELECTION_REVISION = "a" * 40
CLUSTER_UID = "11111111-2222-3333-4444-555555555555"


class ReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.safe_load((DIRECTORY / "release-preflight.yaml").read_text())
        cls.templates = {item["name"]: item for item in cls.workflow["spec"]["templates"]}
        cls.rbac = list(yaml.safe_load_all((DIRECTORY / "preflight-rbac.yaml").read_text()))

    def test_template_is_staged_read_only_and_pins_dependencies(self) -> None:
        self.assertEqual(self.workflow["metadata"]["namespace"], "argo-workflows")
        self.assertEqual(self.workflow["spec"]["serviceAccountName"], "release-preflight")
        self.assertEqual(self.templates["cluster-uid"]["resource"]["action"], "get")
        self.assertEqual(
            self.templates["cluster-uid"]["resource"]["manifest"].splitlines()[3].strip(),
            "name: kube-system",
        )
        self.assertEqual(
            {item["name"] for item in self.workflow["spec"]["arguments"]["parameters"]},
            {"request-revision", "request-path", "request-sha256", "selection-revision"},
        )
        for name in ("validate-schema", "verify-identities"):
            template = self.templates[name]
            container = template.get("container", template.get("script"))
            self.assertIn("@sha256:", container["image"])
            for artifact in template["inputs"]["artifacts"]:
                source = artifact["git"]
                self.assertEqual(
                    source["repo"],
                    "https://github.com/stanimirivanov/perfeng-environment.git",
                )
                self.assertTrue(source["disableSubmodules"])
                self.assertEqual(source["usernameSecret"]["name"], "release-git-read")
                self.assertEqual(source["passwordSecret"]["name"], "release-git-read")
        schema_hash = hashlib.sha256(
            (ROOT / "deployments/release-request-v1.schema.json").read_bytes()
        ).hexdigest()
        self.assertIn(schema_hash, self.templates["validate-schema"]["container"]["args"][0])
        self.assertEqual(
            [item["kind"] for item in self.rbac],
            ["ServiceAccount", "Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding"],
        )
        role = self.rbac[1]["rules"]
        self.assertEqual(
            role[0],
            {
                "apiGroups": ["argoproj.io"],
                "resources": ["workflowtaskresults"],
                "verbs": ["create", "patch"],
            },
        )
        self.assertEqual(
            role[1],
            {
                "apiGroups": [""],
                "resources": ["secrets"],
                "resourceNames": ["release-git-read"],
                "verbs": ["get"],
            },
        )
        self.assertEqual(
            self.rbac[3]["rules"],
            [
                {
                    "apiGroups": [""],
                    "resources": ["namespaces"],
                    "resourceNames": ["kube-system"],
                    "verbs": ["get"],
                }
            ],
        )

    def test_gitops_child_is_manual_and_absent_from_active_root(self) -> None:
        bootstrap = ROOT / "gitops/release-workflows-bootstrap"
        application = yaml.safe_load((bootstrap / "application.yaml").read_text())
        project = yaml.safe_load((bootstrap / "project.yaml").read_text())
        root = yaml.safe_load((ROOT / "gitops/environments/local/kustomization.yaml").read_text())
        self.assertEqual(application["spec"]["project"], project["metadata"]["name"])
        self.assertEqual(application["spec"]["source"]["path"], "gitops/release-workflows")
        self.assertEqual(application["spec"]["destination"]["namespace"], "argo-workflows")
        self.assertNotIn("automated", application["spec"]["syncPolicy"])
        self.assertNotIn("release-workflows-application.yaml", root["resources"])
        self.assertEqual(
            project["spec"]["destinations"],
            [
                {"server": "https://kubernetes.default.svc", "namespace": "argo-workflows"},
                {"server": "https://kubernetes.default.svc", "namespace": "perf-platform"},
                {"server": "https://kubernetes.default.svc", "namespace": "perf-generators"},
            ],
        )

    def execute(self, mutation=None) -> tuple[subprocess.CompletedProcess[str], Path, dict]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        request_root = root / "request"
        selection_root = root / "selection"
        for checkout, revision in (
            (request_root, REQUEST_REVISION),
            (selection_root, SELECTION_REVISION),
        ):
            (checkout / ".git").mkdir(parents=True)
            (checkout / ".git/HEAD").write_text(revision + "\n", encoding="ascii")
        lock = selection_root / "deployments/local-control-plane.json"
        lock.parent.mkdir(parents=True)
        lock.write_bytes(b'{"image":"sha256:pinned"}\n')
        request = {
            "schemaVersion": 1,
            "kind": "PerfengReleaseRequest",
            "environment": {"name": "perfeng-local", "clusterUID": CLUSTER_UID},
            "selection": {
                "repository": "https://github.com/stanimirivanov/perfeng-environment.git",
                "revision": SELECTION_REVISION,
                "gitopsPath": "gitops/environments/local",
                "lockDigests": {
                    "deployments/local-control-plane.json": "sha256:"
                    + hashlib.sha256(lock.read_bytes()).hexdigest()
                },
            },
            "promotion": {
                "expectedCurrent": None,
                "activation": "manual",
                "migration": {"mode": "none"},
            },
        }
        request_path = request_root / "deployments/release-requests/release.json"
        request_path.parent.mkdir(parents=True)
        request_path.write_bytes((json.dumps(request) + "\n").encode())
        environment = {
            **os.environ,
            "REQUEST_ROOT": str(request_root),
            "SELECTION_ROOT": str(selection_root),
            "RESULT_PATH": str(root / "result.json"),
            "REQUEST_REVISION": REQUEST_REVISION,
            "SELECTION_REVISION": SELECTION_REVISION,
            "REQUEST_PATH": "deployments/release-requests/release.json",
            "REQUEST_SHA256": "sha256:" + hashlib.sha256(request_path.read_bytes()).hexdigest(),
            "OBSERVED_CLUSTER_UID": CLUSTER_UID,
        }
        if mutation is not None:
            mutation(environment, request, request_path, lock)
        result = subprocess.run(
            [sys.executable, "-c", self.templates["verify-identities"]["script"]["source"]],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
            timeout=30,
        )
        return result, root / "result.json", environment

    def test_same_request_is_repeatable_and_emits_versioned_result(self) -> None:
        result, output, environment = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(output.read_text(encoding="utf-8"))
        schema = json.loads(
            (ROOT / "deployments/release-preflight-result-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
        self.assertFalse(Draft202012Validator(schema).is_valid({**value, "approved": True}))
        self.assertEqual(value["schemaVersion"], 1)
        self.assertEqual(value["kind"], "PerfengReleasePreflight")
        self.assertEqual(value["requestDigest"], environment["REQUEST_SHA256"])
        self.assertEqual(value["requestPath"], environment["REQUEST_PATH"])
        self.assertEqual(value["clusterUID"], CLUSTER_UID)
        self.assertEqual(value["status"], "identity-verified")

    def test_request_and_cluster_mismatches_fail_without_result(self) -> None:
        def wrong_digest(environment, _request, _path, _lock):
            environment["REQUEST_SHA256"] = "sha256:" + "0" * 64

        def wrong_cluster(environment, _request, _path, _lock):
            environment["OBSERVED_CLUSTER_UID"] = "00000000-2222-3333-4444-555555555555"

        def wrong_revision(environment, _request, _path, _lock):
            environment["SELECTION_REVISION"] = "c" * 40

        def floating_revision(environment, _request, _path, _lock):
            environment["REQUEST_REVISION"] = "main"

        def request_selects_other_commit(environment, request, path, _lock):
            request["selection"]["revision"] = "c" * 40
            path.write_bytes((json.dumps(request) + "\n").encode())
            environment["REQUEST_SHA256"] = (
                "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            )

        def unsafe_path(environment, _request, _path, _lock):
            environment["REQUEST_PATH"] = "../../.git/config"

        for mutate, error in (
            (wrong_digest, "request bytes differ"),
            (wrong_cluster, "cluster UID differs"),
            (wrong_revision, "Git artifact checkout"),
            (floating_revision, "revision must be a full Git commit"),
            (request_selects_other_commit, "selected Git revision differs"),
            (unsafe_path, "request path is outside"),
        ):
            with self.subTest(error=error):
                result, output, _environment = self.execute(mutate)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
                self.assertFalse(output.exists())

    def test_lock_mismatch_and_duplicate_key_fail_closed(self) -> None:
        def changed_lock(_environment, _request, _path, lock):
            lock.write_bytes(b"changed\n")

        def missing_lock(_environment, _request, _path, lock):
            lock.unlink()

        def duplicate_key(environment, _request, path, _lock):
            raw = path.read_text(encoding="utf-8")
            path.write_text(
                raw.replace('"schemaVersion": 1,', '"schemaVersion": 1, "schemaVersion": 1,'),
                encoding="utf-8",
            )
            environment["REQUEST_SHA256"] = (
                "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            )

        for mutate, error in (
            (changed_lock, "deployment lock digest mismatch"),
            (missing_lock, "Git artifact is unavailable"),
            (duplicate_key, "duplicate JSON key"),
        ):
            with self.subTest(error=error):
                result, output, _environment = self.execute(mutate)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
