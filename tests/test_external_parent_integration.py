"""Exercise complete-parent evidence offline, including partial native outputs."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_release_workflows_integration import BASH

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"
SPEC = importlib.util.spec_from_file_location(
    "external_evidence", FIXTURE / "external_parent_evidence.py"
)
assert SPEC is not None and SPEC.loader is not None
EVIDENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVIDENCE)


class ExternalParentIntegrationTests(unittest.TestCase):
    def populate(self, directory, failure):
        parameters = {
            "request-revision": "a" * 40,
            "selection-revision": "b" * 40,
            "request-sha256": "sha256:" + "c" * 64,
            "request-path": "deployments/release-requests/ci-parent-preflight-v1.json",
        }
        cluster = "00000000-0000-0000-0000-000000000001"
        preflight = {
            "schemaVersion": 1,
            "kind": "PerfengReleasePreflight",
            "status": "identity-verified",
            "environment": "perfeng-local",
            "requestRevision": parameters["request-revision"],
            "selectionRevision": parameters["selection-revision"],
            "requestDigest": parameters["request-sha256"],
            "requestPath": parameters["request-path"],
            "clusterUID": cluster,
            "lockDigests": {"deployments/local-control-plane.json": "sha256:" + "d" * 64},
        }
        tasks = {
            "success": set(EVIDENCE.TASKS),
            "preflight": set(),
            "bucket": {"bucket"},
            "database": {"bucket", "database"},
        }[failure]
        result = {}
        root = {
            "phase": "Succeeded" if failure == "success" else "Failed",
            "outputs": {"parameters": []},
        }
        nodes = {"workflow": root}
        if failure != "preflight":
            nodes["preflight"] = {
                "displayName": "preflight",
                "type": "Steps",
                "phase": "Succeeded",
                "outputs": {"parameters": [{"name": "result", "value": json.dumps(preflight)}]},
            }
            nodes["selection"] = {
                "displayName": "task-selection",
                "type": "Pod",
                "phase": "Succeeded",
            }
            result["preflight-result"] = json.dumps(preflight)
        else:
            nodes["identity"] = {
                "displayName": "verify-identities",
                "type": "Pod",
                "phase": "Failed",
                "outputs": {
                    "parameters": [{"name": "result", "valueFrom": {"path": "/tmp/result.json"}}]
                },
            }
        inventory = []
        for task in tasks:
            namespace, output = EVIDENCE.TASKS[task]
            name = {
                "bucket": "perfeng-prepare-bucket-v1-one",
                "database": "perfeng-prepare-db-v1-one",
                "registry": "perfeng-prepare-registry-v1-one",
            }[task]
            job = {
                "metadata": {"namespace": namespace, "name": name, "uid": task + "-uid"},
                "status": {"failed": 1} if task == failure else {"succeeded": 1},
            }
            (directory / f"external-parent-case-{task}.job.json").write_text(json.dumps(job))
            inventory.append(namespace + "/" + name)
            nodes[task] = {
                "displayName": EVIDENCE.STEPS[task],
                "type": "Pod",
                "phase": "Failed" if task == failure else "Succeeded",
            }
            # A created failed Job can have a native name; it is not completion.
            result[output] = name
        root["outputs"]["parameters"] = [
            {"name": name, "value": value} for name, value in result.items()
        ]
        workflow = {
            "metadata": {"name": "workflow"},
            "status": {"phase": root["phase"], "nodes": nodes},
        }
        path = directory / "external-parent-case.workflow.json"
        path.write_text(json.dumps(workflow))
        (directory / "external-parent-case.jobs.txt").write_text(
            "".join(item + "\n" for item in inventory)
        )
        (directory / "parent-first.result.json").write_text(json.dumps(preflight))
        return parameters, cluster, workflow, path

    def test_success_and_expected_partial_failures_match_created_job_identities(self):
        for failure in ("success", "preflight", "bucket", "database"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                parameters, cluster, _, _ = self.populate(directory, failure)
                EVIDENCE.check(directory, "case", failure, parameters, cluster)

    def test_rejects_downstream_execution_after_each_failed_prerequisite(self):
        for failure, downstream in (
            ("preflight", "task-selection"),
            ("bucket", "database-roles"),
            ("database", "execution-registry"),
        ):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                parameters, cluster, workflow, path = self.populate(directory, failure)
                workflow["status"]["nodes"]["unexpected"] = {
                    "displayName": downstream,
                    "type": "Pod",
                    "phase": "Succeeded",
                }
                path.write_text(json.dumps(workflow))
                with self.assertRaises(AssertionError):
                    EVIDENCE.check(directory, "case", failure, parameters, cluster)

    def test_rejects_leaked_downstream_outputs_and_unrelated_jobs(self):
        for change in ("output", "inventory", "job"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                parameters, cluster, workflow, path = self.populate(directory, "bucket")
                if change == "output":
                    workflow["status"]["nodes"]["workflow"]["outputs"]["parameters"].append(
                        {"name": "registry-job", "value": ""}
                    )
                    path.write_text(json.dumps(workflow))
                elif change == "inventory":
                    (directory / "external-parent-case.jobs.txt").write_text(
                        "perf-platform/foreign-job\n"
                    )
                else:
                    workflow["status"]["nodes"]["workflow"]["outputs"]["parameters"][1]["value"] = (
                        "foreign-job"
                    )
                    path.write_text(json.dumps(workflow))
                with self.assertRaises(AssertionError):
                    EVIDENCE.check(directory, "case", "bucket", parameters, cluster)

    def test_rejects_stale_preflight_and_false_success_before_selection(self):
        for change in ("digest", "cluster", "selection", "worker"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                parameters, cluster, workflow, path = self.populate(directory, "success")
                if change == "digest":
                    parameters["request-sha256"] = "sha256:" + "e" * 64
                elif change == "cluster":
                    cluster = "00000000-0000-0000-0000-000000000002"
                elif change == "selection":
                    workflow["status"]["nodes"]["selection"]["phase"] = "Error"
                else:
                    workflow["status"]["nodes"]["registry"]["phase"] = "Failed"
                path.write_text(json.dumps(workflow))
                with self.assertRaises(AssertionError):
                    EVIDENCE.check(directory, "case", "success", parameters, cluster)

    @unittest.skipUnless(BASH, "Bash unavailable")
    def test_shared_fixture_registration_has_no_cluster_side_effects(self):
        assert BASH is not None
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "owned").touch()
            code = """set -euo pipefail
fixture=$1
export KUBECONFIG="$fixture/kubeconfig"
k() { echo 'unexpected cluster operation'; return 99; }
a() { echo 'unexpected workflow operation'; return 99; }
source "$2"
declare -F database_setup bucket_setup sql role_snapshot observer s3 > /dev/null
"""
            completed = subprocess.run(
                [
                    BASH,
                    "-s",
                    "--",
                    directory.as_posix(),
                    (FIXTURE / "external-state-fixtures.sh").as_posix(),
                ],
                input=code,
                text=True,
                capture_output=True,
                timeout=20,
                env={
                    **os.environ,
                    "GITHUB_ACTIONS": "true",
                    "RUNNER_ENVIRONMENT": "github-hosted",
                    "RUNNER_OS": "Linux",
                },
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "")


if __name__ == "__main__":
    unittest.main()
