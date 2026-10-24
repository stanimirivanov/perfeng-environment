"""Complete-parent transport fault evidence must bind the committed API response."""

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from test_adoption_parent_integration import FIXTURE, READINESS, ROOT, evidence
from test_adoption_parent_integration import MODULE as ADOPTION
from test_ledger_response_proxy import PROXY
from test_release_workflows_integration import BASH

SPEC = importlib.util.spec_from_file_location("adoption_response", FIXTURE / "adoption_response.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict(
    sys.modules,
    {
        "adoption_parent_evidence": ADOPTION,
        "readiness_parent_evidence": READINESS,
        "ledger_response_proxy": PROXY,
    },
):
    SPEC.loader.exec_module(MODULE)


def sources():
    return tuple(
        yaml.safe_load((ROOT / "gitops/release-workflows" / name).read_text())
        for name in ("release-adopt-existing-v2.yaml", "adoption-records.yaml")
    )


def failure():
    args = list(evidence(True))[:6]
    workflow, _, _, _, _, api = args
    workflow["spec"]["workflowTemplateRef"]["name"] = MODULE.NAME
    workflow["status"]["phase"] = "Error"
    workflow["status"]["storedWorkflowTemplateSpec"] = MODULE.template(*sources())["spec"]
    nodes = workflow["status"]["nodes"]
    nodes["adoption"].update(phase="Error", outputs={})
    nodes["create-receipt"].update(phase="Error", outputs={}, templateName="fault-create-receipt")
    nodes["barrier"] = {
        "id": "barrier",
        "displayName": MODULE.BARRIER,
        "type": "Suspend",
        "phase": "Succeeded",
    }
    for name in (
        "stored-receipt",
        "root-after",
        "pointer-plan",
        "create-pointer",
        "stored-pointer",
        "final-receipt",
        "final-root",
        "finish",
    ):
        del nodes[name]
    api["pointer"] = None
    expected = copy.deepcopy(api["receipt"])
    for key in ("uid", "resourceVersion"):
        del expected["metadata"][key]
    workers = [
        {
            "nodeID": "create-receipt",
            "account": "release-adoption",
            "uid": "writer-uid",
            "exitCode": 1,
        }
    ]
    events = [
        {
            "event": "receipt-committed-response-dropped",
            "upstreamStatus": 201,
            "uid": api["receipt"]["metadata"]["uid"],
            "contentDigest": PROXY.digest(expected),
            "successfulResponsesForwarded": 0,
        }
    ]
    return args + [workers, events, expected]


class AdoptionResponseLossTests(unittest.TestCase):
    def test_generated_parent_reverses_to_exact_published_composition(self):
        parent, native = sources()
        original = copy.deepcopy(parent)
        generated = MODULE.template(parent, native)
        self.assertEqual(parent, original)
        spec = copy.deepcopy(generated["spec"])
        barrier, resource = spec["templates"][1:]
        self.assertEqual(barrier, {"name": MODULE.BARRIER, "suspend": {}})
        self.assertEqual(resource["activeDeadlineSeconds"], 60)
        self.assertFalse(resource["resource"]["setOwnerReference"])
        manifest = yaml.safe_load(resource["resource"]["manifest"])
        canonical = next(t for t in native["spec"]["templates"] if t["name"] == "create-receipt")
        self.assertEqual(manifest, yaml.safe_load(canonical["resource"]["manifest"]))
        restored_resource = copy.deepcopy(resource)
        restored_resource["name"] = "create-receipt"
        del restored_resource["podSpecPatch"]
        del restored_resource["resource"]["flags"]
        restored_resource["resource"]["manifest"] = canonical["resource"]["manifest"]
        self.assertEqual(restored_resource, canonical)
        spec["templates"] = spec["templates"][:1]
        steps = spec["templates"][0]["steps"]
        index = next(i for i, group in enumerate(steps) if group[0]["name"] == MODULE.BARRIER)
        del steps[index]
        create = steps[index][0]
        self.assertEqual(create.pop("template"), "fault-create-receipt")
        create["templateRef"] = {"name": native["metadata"]["name"], "template": "create-receipt"}
        self.assertEqual(spec, parent["spec"])

    def test_committed_response_matches_actual_plan_and_failed_worker(self):
        args = failure()
        self.assertEqual(MODULE.expected_receipt(args[0], args[3]), args[-1])
        result = MODULE.verify(*args)
        self.assertEqual(result["receiptUID"], "receipt-uid")
        self.assertEqual(result["workflowUID"], args[0]["metadata"]["uid"])

    def test_controller_serialization_defaults_preserve_strict_composition_check(self):
        args = failure()
        templates = args[0]["status"]["storedWorkflowTemplateSpec"]["templates"]
        # Observed in real Argo v4.1.5 storedWorkflowTemplateSpec evidence: Go
        # emits these empty struct fields and omits the false resource default.
        for template in templates:
            for key in ("inputs", "outputs", "metadata"):
                template.setdefault(key, {})
            for group in template.get("steps", []):
                for step in group:
                    step.setdefault("arguments", {})
            template.get("resource", {}).pop("setOwnerReference", None)
        MODULE.verify(*args)
        templates[-1]["resource"]["setOwnerReference"] = True
        with self.assertRaises(AssertionError):
            MODULE.verify(*args)

    def test_transport_failure_alone_cannot_qualify(self):
        for change in (
            "no-event",
            "denied",
            "uid",
            "digest",
            "forwarded",
            "extra-event",
            "pointer",
            "bytes",
            "origin",
            "worker",
            "exit",
            "timeout",
            "account",
            "result",
            "downstream",
            "readiness",
            "barrier",
            "composition",
            "parent",
            "plan",
        ):
            with self.subTest(change=change):
                args = failure()
                workflow, _, _, _, _, api, workers, events, expected = args
                nodes = workflow["status"]["nodes"]
                if change == "no-event":
                    events.clear()
                elif change == "denied":
                    events[0]["upstreamStatus"] = 403
                elif change == "uid":
                    events[0]["uid"] = "foreign"
                elif change == "digest":
                    events[0]["contentDigest"] = "foreign"
                elif change == "forwarded":
                    events[0]["successfulResponsesForwarded"] = 1
                elif change == "extra-event":
                    events.append({"event": "upstream-failed"})
                elif change == "pointer":
                    api["pointer"] = {"metadata": {"uid": "unexpected"}}
                elif change == "bytes":
                    api["receipt"]["binaryData"]["request.json"] = "eA=="
                elif change == "origin":
                    expected["metadata"]["annotations"]["perfeng.io/first-workflow-uid"] = "foreign"
                elif change == "worker":
                    workers[0]["nodeID"] = "plan"
                elif change == "exit":
                    workers[0]["exitCode"] = 0
                elif change == "timeout":
                    workers[0]["exitCode"] = 137
                elif change == "account":
                    workers[0]["account"] = "default"
                elif change == "result":
                    nodes["adoption"]["outputs"] = {
                        "parameters": [{"name": "result", "value": "{}"}]
                    }
                elif change == "downstream":
                    nodes["create-pointer"] = {
                        "displayName": "create-pointer",
                        "type": "Pod",
                        "phase": "Failed",
                    }
                elif change == "readiness":
                    nodes["readiness"]["phase"] = "Failed"
                elif change == "barrier":
                    nodes["barrier"]["phase"] = "Running"
                elif change == "composition":
                    workflow["status"]["storedWorkflowTemplateSpec"]["serviceAccountName"] = (
                        "default"
                    )
                elif change == "parent":
                    workflow["spec"]["workflowTemplateRef"]["name"] = "foreign"
                else:
                    nodes["plan"]["outputs"]["parameters"][0]["value"] = "foreign"
                with self.assertRaises((AssertionError, KeyError, ValueError)):
                    MODULE.verify(*args)

    def test_fixture_is_main_only_and_recovery_uses_normal_parent(self):
        shell = (FIXTURE / "adoption-response.sh").read_text()
        self.assertIn("${scenario:-} == response-loss", shell)
        self.assertIn("-z ${GITHUB_TOKEN:-}", shell)
        self.assertLess(
            shell.index('a resume "$response_workflow"'), shell.index("adoption_case response-loss")
        )
        self.assertLess(
            shell.index("adoption_case response-loss"),
            shell.index("adoption_case recovered success"),
        )
        self.assertNotIn("--request-timeout", shell)
        parent = (FIXTURE / "adoption-parent.sh").read_text()
        self.assertIn('source "$source_dir/adoption-response.sh"', parent)
        self.assertIn("--from workflowtemplate/perfeng-release-adopt-existing-v2", parent)

    @unittest.skipUnless(BASH and shutil.which("jq"), "Bash and jq required for barrier polling")
    def test_barrier_poll_allows_uninitialized_status_and_refuses_terminal_phase(self):
        assert BASH is not None
        shell = (FIXTURE / "adoption-response.sh").read_text()
        polling = (
            "response_deadline="
            + shell.split("response_deadline=", 1)[1].split("\nPYTHONPATH=", 1)[0]
        )
        for phase, succeeds in (
            (None, True),
            ("", True),
            ("Pending", True),
            ("Running", True),
            ("Failed", False),
            ("Error", False),
            ("Succeeded", False),
        ):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                first = {"status": {"phase": phase}} if phase is not None else {}
                second = {
                    "status": {
                        "phase": "Running",
                        "nodes": {
                            "barrier": {
                                "displayName": MODULE.BARRIER,
                                "type": "Suspend",
                                "phase": "Running",
                            }
                        },
                    }
                }
                (directory / "state-0.json").write_text(json.dumps(first))
                (directory / "state-1.json").write_text(json.dumps(second))
                (directory / "count").write_text("0")
                script = (
                    """set -euo pipefail
fixture=$1
response_workflow=fixture
k() { local index; index=$(cat "$fixture/count"); echo $((index+1)) > "$fixture/count"; cat "$fixture/state-$index.json"; }
sleep() { :; }
"""
                    + polling
                )
                result = subprocess.run(
                    [BASH, "-c", script, "barrier-test", directory.as_posix()],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                self.assertEqual(
                    (directory / "count").read_text().strip(), "2" if succeeds else "1"
                )

    def test_cli_binds_barrier_plan_and_failed_parent_to_safe_report(self):
        args = failure()
        workflow, parameters, preflight, raw, _, api, workers, events, expected = args
        barrier = copy.deepcopy(workflow)
        barrier["status"]["phase"] = "Running"
        barrier["status"]["nodes"]["barrier"]["phase"] = "Running"
        del barrier["status"]["nodes"]["create-receipt"]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, value in (
                ("adoption-response-barrier.workflow.json", barrier),
                ("adoption-parent-response-loss.workflow.json", workflow),
                ("adoption-parent-response-loss.workers.json", workers),
                ("parent-parameters.json", parameters),
                ("parent-first.result.json", preflight),
                ("adoption-parent-api.json", api),
                ("ledger-response-expected.json", expected),
            ):
                (directory / name).write_text(json.dumps(value), encoding="utf-8")
            (directory / "parent-request.json").write_bytes(raw)
            (directory / "ledger-response-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events), encoding="utf-8"
            )

            def run(mode):
                return subprocess.run(
                    [
                        sys.executable,
                        str(FIXTURE / "adoption_response.py"),
                        mode,
                        str(directory),
                        str(directory),
                    ],
                    cwd=ROOT,
                    env={**os.environ, "PYTHONPATH": str(ROOT)},
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=True,
                )

            generated = json.loads(run("template").stdout)
            self.assertEqual(generated, MODULE.template(*sources()))
            self.assertEqual(json.loads(run("expected").stdout), expected)
            run("verify")
            self.assertEqual(
                json.loads((directory / "adoption-response-loss.outcome.json").read_text()),
                MODULE.verify(*args),
            )
            barrier["status"]["nodes"]["barrier"]["phase"] = "Succeeded"
            (directory / "adoption-response-barrier.workflow.json").write_text(
                json.dumps(barrier), encoding="utf-8"
            )
            with self.assertRaises(subprocess.CalledProcessError):
                run("expected")


if __name__ == "__main__":
    unittest.main()
