"""Check complete-parent evidence boundaries offline; live execution stays in CI."""

import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"
JQ = shutil.which("jq")


def refused_workflow(step):
    """Model Argo's unresolved output declarations alongside a nested result."""
    unresolved = {"parameters": [{"name": "result", "valueFrom": {"path": "/tmp/checkpoint.json"}}]}
    value = {
        "metadata": {"name": "maintenance"},
        "status": {
            "phase": "Failed",
            "nodes": {
                "maintenance": {
                    "displayName": "maintenance",
                    "type": "Steps",
                    "phase": "Failed",
                    "outputs": copy.deepcopy(unresolved),
                },
                "failed": {
                    "displayName": step,
                    "type": "Pod",
                    "phase": "Failed",
                    "outputs": copy.deepcopy(unresolved),
                },
            },
        },
    }
    if step == "observe":
        value["status"]["nodes"]["preflight"] = {
            "displayName": "preflight",
            "type": "Steps",
            "phase": "Succeeded",
            "outputs": {
                "parameters": [
                    {
                        "name": "result",
                        "value": json.dumps(
                            {"kind": "PerfengReleasePreflight", "status": "identity-verified"}
                        ),
                    }
                ]
            },
        }
    return value


class MaintenanceParentIntegrationTests(unittest.TestCase):
    def test_actual_parent_and_ci_phase_preserve_production_composition(self):
        parent = yaml.safe_load(
            (ROOT / "gitops/release-workflows/release-maintenance-checkpoint-v3.yaml").read_text()
        )
        steps = parent["spec"]["templates"][0]["steps"]
        self.assertEqual([step[0]["name"] for step in steps], ["preflight", "observe"])
        self.assertEqual(parent["spec"]["serviceAccountName"], "release-maintenance-checkpoint")
        source = (FIXTURE / "maintenance-parent.sh").read_text()
        self.assertIn("--from workflowtemplate/" + parent["metadata"]["name"], source)
        self.assertNotIn("--from workflowtemplate/perfeng-verify-release-quiescence", source)
        self.assertNotIn("-p preflight-result", source)
        self.assertNotIn("argo retry", source)
        self.assertIn('cmp "$results/parent-first.result.json"', source)
        self.assertIn(
            'source "$source_dir/maintenance-parent.sh"',
            (FIXTURE / "preflight-parent.sh").read_text(),
        )
        self.assertNotIn(
            'source "$source_dir/maintenance-parent.sh"', (FIXTURE / "run.sh").read_text()
        )
        self.assertIn(parent["metadata"]["name"], (FIXTURE / "run.sh").read_text())
        cases = re.findall(r"^maintenance_parent_case (\S+) \S+ (\S+)", source, re.M)
        self.assertEqual(
            cases,
            [
                ("first", "success"),
                ("repeat", "success"),
                ("wrong-digest", "verify-identities"),
                ("unpaused", "observe"),
                ("missing-read-grant", "observe"),
                ("corrected", "success"),
            ],
        )

    def test_checkpoint_checker_binds_request_cluster_and_current_resource_versions(self):
        code = re.findall(
            r"<<'PY'\n(.*?)\nPY", (FIXTURE / "maintenance-parent.sh").read_text(), re.S
        )[0]
        parameters = {
            "request-revision": "a" * 40,
            "selection-revision": "b" * 40,
            "request-sha256": "sha256:" + "c" * 64,
        }
        resources = [
            {
                "kind": kind,
                "namespace": namespace,
                "name": name,
                "uid": name + "-uid",
                "resourceVersion": "12",
            }
            for kind, namespace, name in (
                ("Application", "argocd", "perfeng-local-root"),
                ("Application", "argocd", "perfeng-local-control-plane"),
                ("Deployment", "perf-platform", "control-plane"),
            )
        ]
        cluster = "00000000-0000-0000-0000-000000000001"
        result = {
            "schemaVersion": 1,
            "kind": "PerfengMaintenanceCheckpoint",
            "status": "quiescence-observed",
            "environment": "perfeng-local",
            "requestDigest": parameters["request-sha256"],
            "requestRevision": parameters["request-revision"],
            "selectionRevision": parameters["selection-revision"],
            "clusterUID": cluster,
            "podListResourceVersion": "13",
            "resources": resources,
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            inputs = [
                directory / name for name in ("parameters.json", "result.json", "observations.json")
            ]
            inputs[0].write_text(json.dumps(parameters))
            inputs[2].write_text(
                json.dumps(sorted(resources, key=lambda value: (value["kind"], value["name"])))
            )
            for field in (
                None,
                "requestDigest",
                "requestRevision",
                "selectionRevision",
                "clusterUID",
                "uid",
                "resourceVersion",
                "podListResourceVersion",
            ):
                with self.subTest(field=field):
                    candidate = json.loads(json.dumps(result))
                    if field in {"uid", "resourceVersion"}:
                        candidate["resources"][0][field] = "changed"
                    elif field == "podListResourceVersion":
                        candidate[field] = ""
                    elif field is not None:
                        candidate[field] = {
                            "requestDigest": "sha256:" + "d" * 64,
                            "requestRevision": "d" * 40,
                            "selectionRevision": "d" * 40,
                            "clusterUID": "00000000-0000-0000-0000-000000000002",
                        }[field]
                    inputs[1].write_text(json.dumps(candidate))
                    completed = subprocess.run(
                        [sys.executable, "-", *map(str, inputs), cluster],
                        input=code,
                        cwd=ROOT,
                        text=True,
                        capture_output=True,
                        timeout=20,
                    )
                    if field is None:
                        self.assertEqual(completed.returncode, 0, completed.stderr)
                    else:
                        self.assertNotEqual(completed.returncode, 0)

    def test_nested_preflight_is_allowed_but_checkpoint_or_downstream_execution_is_refused(self):
        self.assertIsNotNone(JQ, "Linux CI must provide jq")
        assert JQ is not None
        cases = []
        for step in ("observe", "verify-identities"):
            cases.append((step, refused_workflow(step), True))
            for leaked in ("", None, json.dumps({"kind": "PerfengMaintenanceCheckpoint"})):
                value = refused_workflow(step)
                value["status"]["nodes"]["maintenance"]["outputs"]["parameters"][0]["value"] = (
                    leaked
                )
                cases.append((step, value, False))
            value = refused_workflow(step)
            value["status"]["nodes"]["failed"]["phase"] = "Error"
            cases.append((step, value, False))
            value = refused_workflow(step)
            value["status"]["phase"] = "Error"
            cases.append((step, value, False))
        value = refused_workflow("verify-identities")
        value["status"]["nodes"]["downstream"] = {
            "displayName": "observe",
            "type": "Pod",
            "phase": "Succeeded",
        }
        cases.append(("verify-identities", value, False))
        value = refused_workflow("observe")
        del value["status"]["nodes"]["preflight"]
        cases.append(("observe", value, False))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workflow.json"
            for number, (step, value, expected) in enumerate(cases):
                with self.subTest(case=number):
                    path.write_text(json.dumps(value))
                    completed = subprocess.run(
                        [
                            JQ,
                            "-e",
                            "--arg",
                            "step",
                            step,
                            "-f",
                            str(FIXTURE / "maintenance-parent-refusal.jq"),
                            str(path),
                        ],
                        text=True,
                        capture_output=True,
                        timeout=20,
                    )
                    self.assertNotEqual(completed.returncode, 3, completed.stderr)
                    self.assertEqual(completed.returncode == 0, expected, completed.stderr)


if __name__ == "__main__":
    unittest.main()
