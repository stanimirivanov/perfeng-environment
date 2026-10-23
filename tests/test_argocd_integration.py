"""Offline authentication, immutable-source and controller-observation boundaries."""

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml
from test_release_workflows_integration import BASH

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"
SPEC = importlib.util.spec_from_file_location("argocd_inputs", FIXTURE / "argocd_inputs.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
JQ = shutil.which("jq")


class ArgoCDIntegrationTests(unittest.TestCase):
    def test_seaweedfs_uses_only_reviewed_pin_and_automation_overrides(self):
        path = ROOT / MODULE.FOUNDATION_APPLICATIONS["seaweedfs"]
        raw = path.read_bytes()
        expected = yaml.safe_load(raw)
        expected["spec"]["source"]["targetRevision"] = "a" * 40
        expected["spec"]["syncPolicy"]["automated"] = {
            "enabled": False,
            "prune": False,
            "selfHeal": False,
        }
        self.assertEqual(MODULE.foundation_application("a" * 40, "seaweedfs"), expected)
        self.assertEqual(path.read_bytes(), raw)
        for workload in ("../seaweedfs", "root", "control-plane", ""):
            with self.subTest(workload=workload), self.assertRaises(ValueError):
                MODULE.foundation_application("a" * 40, workload)
        for revision in ("main", "a" * 39, "A" * 40, "a" * 40 + "\n"):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                MODULE.foundation_application(revision, "seaweedfs")

    def test_seaweedfs_convergence_requires_its_own_source_uid_and_fresh_operation(self):
        revision = "a" * 40
        spec = MODULE.foundation_application(revision, "seaweedfs")["spec"]
        original: dict[str, Any] = {
            "metadata": {"uid": "owned-seaweedfs"},
            "spec": spec,
            "status": {
                "sync": {
                    "status": "Synced",
                    "revision": revision,
                    "comparedTo": {"source": spec["source"], "destination": spec["destination"]},
                },
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "syncResult": {"revision": revision, "source": spec["source"]},
                    "operation": {
                        "info": [{"name": "ci-case", "value": "repeat"}],
                        "sync": {"revision": revision, "syncStrategy": {"apply": {}}},
                    },
                },
            },
        }
        MODULE.foundation_converged(original, "owned-seaweedfs", "repeat", revision, "seaweedfs")
        for field, replacement in (
            ("metadata.uid", "foreign"),
            ("spec.source.path", "charts/postgres"),
            ("spec.syncPolicy.automated.enabled", True),
            ("status.sync.status", "OutOfSync"),
            ("status.sync.revision", "b" * 40),
            ("status.health.status", "Progressing"),
            ("status.conditions", [{"type": "ComparisonError"}]),
            ("status.sync.comparedTo.source", {}),
            ("status.operationState.phase", "Failed"),
            ("status.operationState.syncResult.source", {}),
            ("status.operationState.operation.info", []),
            ("status.operationState.operation.sync.manifests", ["local-override"]),
        ):
            with self.subTest(field=field):
                value = copy.deepcopy(original)
                target = value
                *parents, key = field.split(".")
                for parent in parents:
                    target = target[parent]
                target[key] = replacement
                with self.assertRaises(AssertionError):
                    MODULE.foundation_converged(
                        value, "owned-seaweedfs", "repeat", revision, "seaweedfs"
                    )

    def test_environment_secret_is_exact_url_and_other_sources_are_refused(self):
        secret = MODULE.repository_secret("private-test-value", MODULE.ENVIRONMENT_REPOSITORY)
        self.assertEqual(secret["metadata"]["name"], "ci-perfeng-environment-repository")
        self.assertEqual(secret["stringData"]["url"], MODULE.ENVIRONMENT_REPOSITORY)
        self.assertEqual(
            secret["metadata"]["labels"], {"argocd.argoproj.io/secret-type": "repository"}
        )
        self.assertTrue(secret["immutable"])
        for source in (
            "https://github.com/stanimirivanov/",
            MODULE.ENVIRONMENT_REPOSITORY + "/",
            "https://github.com/stanimirivanov/other.git",
            "ssh://git@github.com/stanimirivanov/perfeng-environment.git",
        ):
            with self.subTest(source=source), self.assertRaises(ValueError):
                MODULE.repository_secret("private-test-value", source)

    def test_postgres_ci_overrides_are_only_pin_and_disabled_automation(self):
        path = ROOT / MODULE.POSTGRES_APPLICATION
        original = path.read_bytes()
        expected = yaml.safe_load(original)
        expected["spec"]["source"]["targetRevision"] = "a" * 40
        expected["spec"]["syncPolicy"]["automated"] = {
            "enabled": False,
            "prune": False,
            "selfHeal": False,
        }
        self.assertEqual(MODULE.postgres_application("a" * 40), expected)
        self.assertEqual(path.read_bytes(), original)
        for revision in ("main", "a" * 39, "A" * 40, "a" * 40 + "\n"):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                MODULE.postgres_application(revision)

    def test_postgres_convergence_refuses_wrong_source_stale_operation_and_partial_sync(self):
        revision = "a" * 40
        spec = MODULE.postgres_application(revision)["spec"]
        original: dict[str, Any] = {
            "metadata": {"uid": "owned-postgres"},
            "spec": spec,
            "status": {
                "sync": {
                    "status": "Synced",
                    "revision": revision,
                    "comparedTo": {"source": spec["source"], "destination": spec["destination"]},
                },
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "syncResult": {"revision": revision, "source": spec["source"]},
                    "operation": {
                        "info": [{"name": "ci-case", "value": "repeat"}],
                        "sync": {"revision": revision, "syncStrategy": {"apply": {}}},
                    },
                },
            },
        }
        MODULE.postgres_converged(original, "owned-postgres", "repeat", revision)
        for field, replacement in (
            ("metadata.uid", "foreign"),
            ("spec.source.targetRevision", "main"),
            ("spec.syncPolicy.automated.enabled", True),
            ("status.sync.status", "OutOfSync"),
            ("status.sync.revision", "b" * 40),
            ("status.health.status", "Progressing"),
            ("status.conditions", [{"type": "ComparisonError"}]),
            ("status.sync.comparedTo.source", {}),
            ("status.sync.comparedTo.destination", {}),
            ("status.operationState.phase", "Failed"),
            ("status.operationState.syncResult.revision", "b" * 40),
            ("status.operationState.syncResult.source", {}),
            ("status.operationState.operation.info", []),
            ("status.operationState.operation.sync.manifests", ["local-override"]),
            ("status.operationState.operation.sync.prune", True),
            ("operation", {"sync": {}}),
        ):
            with self.subTest(field=field):
                value = copy.deepcopy(original)
                target = value
                *parents, key = field.split(".")
                for parent in parents:
                    target = target[parent]
                target[key] = replacement
                with self.assertRaises(AssertionError):
                    MODULE.postgres_converged(value, "owned-postgres", "repeat", revision)

    @unittest.skipUnless(JQ, "jq required")
    def test_shared_sync_request_preserves_single_and_multi_source_revision_contracts(self):
        assert JQ is not None
        expression = re.search(
            r"jq --arg label \"\$label\" --arg revision \"\$revision\" '(.+?)' > \"\$fixture/argocd-sync.json\"",
            (FIXTURE / "argocd.sh").read_text(),
            re.S,
        )
        assert expression
        for application in (
            yaml.safe_load((ROOT / MODULE.APPLICATION).read_text()),
            MODULE.postgres_application("a" * 40),
            MODULE.foundation_application("a" * 40, "seaweedfs"),
            yaml.safe_load((ROOT / "gitops/bootstrap/local-root-application.yaml").read_text()),
        ):
            with self.subTest(application=application["metadata"]["name"]):
                application["metadata"]["resourceVersion"] = "23"
                completed = subprocess.run(
                    [JQ, "--arg", "label", "repeat", "--arg", "revision", "b" * 40, expression[1]],
                    input=json.dumps(application),
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                request = json.loads(completed.stdout)
                self.assertEqual(request["metadata"], {"resourceVersion": "23"})
                self.assertEqual(
                    request["operation"]["info"], [{"name": "ci-case", "value": "repeat"}]
                )
                spec = application["spec"]
                selected = (
                    {"revisions": [s["targetRevision"] for s in spec["sources"]]}
                    if "sources" in spec
                    else {"revision": spec["source"]["targetRevision"]}
                )
                if application["metadata"]["name"] == "perfeng-local-root":
                    selected = {"revision": "b" * 40}
                self.assertEqual(
                    request["operation"]["sync"], {**selected, "syncStrategy": {"apply": {}}}
                )

    def test_bootstrap_reuses_validation_and_refuses_wrong_head_or_dirty_source(self):
        lock = MODULE.infrastructure.load_lock()
        for head, dirty in ((lock.revision, b""), ("0" * 40, b""), (lock.revision, b" M file")):
            with (
                self.subTest(head=head, dirty=dirty),
                patch.object(MODULE.infrastructure, "validate", return_value=lock) as validate,
                patch.object(MODULE.infrastructure, "git", side_effect=[head.encode(), dirty]),
            ):
                if head == lock.revision and not dirty:
                    self.assertEqual(
                        MODULE.bootstrap(Path("source")),
                        Path("source") / Path(lock.argocd_bootstrap).parent,
                    )
                else:
                    with self.assertRaises(ValueError):
                        MODULE.bootstrap(Path("source"))
                validate.assert_called_once_with(infrastructure_root=Path("source"))

    def test_repository_secret_is_immutable_exact_url_and_rejects_missing_credentials(self):
        secret = MODULE.repository_secret("private-test-value")
        self.assertEqual(secret["metadata"]["namespace"], "argocd")
        self.assertEqual(
            secret["metadata"]["labels"], {"argocd.argoproj.io/secret-type": "repository"}
        )
        self.assertTrue(secret["immutable"])
        self.assertEqual(
            secret["stringData"],
            {
                "type": "git",
                "url": "https://github.com/stanimirivanov/perfeng-k8s.git",
                "username": "x-access-token",
                "password": "private-test-value",
            },
        )
        for token in ("", " token", "token\n", "token\rsecret"):
            with self.subTest(token=repr(token)), self.assertRaises(ValueError):
                MODULE.repository_secret(token)

    def test_convergence_refuses_stale_operations_overrides_and_incomplete_status(self):
        spec = yaml.safe_load((ROOT / MODULE.APPLICATION).read_text())["spec"]
        revisions = [s["targetRevision"] for s in spec["sources"]]
        original: dict[str, Any] = {
            "metadata": {"uid": "owned-application"},
            "spec": spec,
            "status": {
                "sync": {
                    "status": "Synced",
                    "comparedTo": {"sources": spec["sources"], "destination": spec["destination"]},
                    "revisions": revisions,
                },
                "health": {"status": "Healthy"},
                "operationState": {
                    "phase": "Succeeded",
                    "syncResult": {"revisions": revisions},
                    "operation": {
                        "info": [{"name": "ci-case", "value": "repeat"}],
                        "sync": {"revisions": revisions, "syncStrategy": {"apply": {}}},
                    },
                },
            },
        }
        changes = [
            ("metadata.uid", "foreign"),
            ("spec.syncPolicy.automated.enabled", True),
            ("status.sync.status", "OutOfSync"),
            ("status.health.status", "Progressing"),
            ("status.conditions", [{"type": "ComparisonError"}]),
            ("status.sync.comparedTo.sources", []),
            ("status.sync.comparedTo.destination", {}),
            ("status.sync.revisions", ["0" * 40]),
            ("status.operationState.phase", "Failed"),
            ("status.operationState.syncResult.revisions", []),
            ("status.operationState.operation.info", [{"name": "ci-case", "value": "first"}]),
            ("status.operationState.operation.sync.manifests", ["local-override"]),
            ("status.operationState.operation.sync.prune", True),
            ("operation", {"sync": {}}),
        ]
        MODULE.converged(original, "owned-application", "repeat")
        for field, replacement in changes:
            with self.subTest(field=field):
                value = copy.deepcopy(original)
                target = value
                *parents, key = field.split(".")
                for parent in parents:
                    target = target[parent]
                target[key] = replacement
                with self.assertRaises(AssertionError):
                    MODULE.converged(value, "owned-application", "repeat")

    def test_credential_is_staged_privately_and_only_main_has_new_exposure(self):
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/release-preflight-main.yml").read_text()
        )
        job = workflow["jobs"]["preflight-parent"]
        self.assertIn("refs/heads/main", job["if"])
        self.assertEqual(job["permissions"], {"contents": "write", "packages": "read"})
        checkout = next(
            s["with"]
            for s in job["steps"]
            if s.get("with", {}).get("repository") == "stanimirivanov/k8s-infrastructure"
        )
        self.assertEqual(checkout["ref"], MODULE.infrastructure.load_lock().revision)
        self.assertEqual(checkout["path"], ".ci/k8s-infrastructure")
        for option in ("persist-credentials", "submodules", "lfs"):
            self.assertFalse(checkout[option])
        stage = next(s for s in job["steps"] if "ARGOCD_SOURCE_TOKEN" in s.get("env", {}))
        self.assertEqual(
            stage["env"]["ARGOCD_SOURCE_TOKEN"],
            "${{ secrets.PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN }}",
        )
        run = next(s for s in job["steps"] if s.get("run") == "make release-workflows-integration")
        self.assertNotIn("ARGOCD_SOURCE_TOKEN", run["env"])
        cleanup = next(
            s
            for s in job["steps"]
            if s.get("name") == "Remove private Argo CD repository credential"
        )
        self.assertEqual(cleanup["if"], "always()")
        with tempfile.TemporaryDirectory() as temporary:
            code = stage["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
            with patch.dict(
                os.environ, {"RUNNER_TEMP": temporary, "ARGOCD_SOURCE_TOKEN": "test-only"}
            ):
                exec(compile(code, "credential staging", "exec"), {})
                with self.assertRaises(FileExistsError):
                    exec(compile(code, "credential staging", "exec"), {})
            self.assertEqual((Path(temporary) / "perfeng-argocd-token").read_text(), "test-only")
        pr = (ROOT / ".github/workflows/release-workflows-kind.yml").read_text()
        self.assertNotIn("PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN", pr)
        self.assertNotIn("ARGOCD_SOURCE_TOKEN", pr)

    @unittest.skipUnless(BASH, "Bash required")
    def test_phase_refuses_standalone_invocation_and_reuses_native_observer(self):
        assert BASH is not None
        result = subprocess.run(
            [BASH, (FIXTURE / "argocd.sh").as_posix()], capture_output=True, timeout=20
        )
        self.assertEqual(result.returncode, 2)
        workflow = yaml.safe_load((FIXTURE / "observer-workflow.yaml").read_text())
        observer = next(t for t in workflow["spec"]["templates"] if t["name"] == "application")
        self.assertEqual(
            observer["steps"][0][0]["templateRef"],
            {
                "name": "perfeng-observe-release-readiness-v1",
                "template": "application",
            },
        )
        source = (FIXTURE / "argocd.sh").read_text()
        self.assertNotIn("--force", source)
        self.assertNotIn("--prune", source)
        self.assertNotIn(
            "patch application perfeng-local-control-plane --subresource=status", source
        )
        self.assertIn("argocd_case corrected", source)
        chain = (FIXTURE / "preflight-parent.sh").read_text()
        self.assertLess(
            chain.index('source "$source_dir/server.sh"'),
            chain.index('source "$source_dir/argocd.sh"'),
        )


if __name__ == "__main__":
    unittest.main()
