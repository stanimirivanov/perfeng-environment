"""Offline authentication, immutable-source and controller-observation boundaries."""

import copy
import importlib.util
import os
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


class ArgoCDIntegrationTests(unittest.TestCase):
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
